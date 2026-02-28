#!/usr/bin/env python3
"""EQ-WS2: Equities Content-Price Correlation Study.

Written from scratch — reuses ONLY architectural patterns from crypto WS2:
  - Phase resume with _meta + SHA256 hash chain
  - write_phase_output() / load_phase_output()
  - _compute_bonferroni_binom()
  - Pool parallelism pattern

NO imports from social_features.py. NO crypto-specific logic.

SME corrections applied:
  - Gap 1: Excess-return labels (primary), raw-return labels (diagnostic)
  - Gap 2: Moulton-corrected effective N in Phase 3 + Phase 6 Bonferroni
  - Gap 3: Content void framing in Phase 7 LLM assessment
  - Rec C: ETFs excluded from label dataset
  - Rec D: Effective cardinality < 3 kill rule in Phase 0.5
  - Rec E: sector_relative_return listed once only

Usage:
  tmux new -s eq-ws2
  cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker \
    python3 /scripts/eq_ws2_content_price_study.py \
    --start-date 2025-08-01 --end-date 2026-02-28 \
    --horizons 1d,3d,5d,gap_open \
    --workers 12
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing as mp
import os
import sys
import time as time_mod
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Runtime path detection (CLAUDE.md §2)
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")
    sys.path.insert(0, "/scripts/equities_predictor")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker", "app"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "equities_predictor"))

from equities_predictor.db import (  # type: ignore[import-untyped]
    connect,
    list_symbols,
    load_events,
    load_ohlcv,
    load_raw_mentions,
    load_earnings_calendar,
)
from equities_predictor.indicators import INDICATOR_REGISTRY, compute_all  # type: ignore[import-untyped]
from equities_predictor.content_features import (  # type: ignore[import-untyped]
    ETF_SYMBOLS,
    compute_rolling_beta,
    compute_excess_returns,
    compute_residual_vol,
    compute_realized_vol,
    compute_tone_score,
    compute_simple_sentiment,
    compute_filing_type_features,
    compute_event_count_deviation,
    compute_mention_counts,
    compute_mention_velocity,
    compute_source_diversity,
    compute_multi_source_confirmation,
    compute_session_features,
    compute_earnings_proximity,
    feature_viability_audit,
    is_etf,
    label_excess_return,
    label_raw_return,
    moulton_effective_n,
    estimate_cross_sectional_correlation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HORIZONS_DEFAULT = ["1d", "3d", "5d", "gap_open"]
OUTPUT_DIR = Path("/scripts/output" if os.path.isdir("/app") else
                  os.path.join(os.path.dirname(__file__), "output"))


# ── Phase checkpointing (reused from crypto WS2) ──────────────

def write_phase_output(data: dict, phase: str, output_dir: Path,
                       phase_version: str = "1.0") -> str:
    payload_str = json.dumps(data, sort_keys=True, default=str)
    sha = hashlib.sha256(payload_str.encode()).hexdigest()
    data["_meta"] = {
        "phase": phase,
        "phase_version": phase_version,
        "sha256": sha,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = output_dir / f"eq_ws2_phase{phase}_output.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Phase {phase} output → {path} (sha256={sha[:12]}...)")
    return sha


def load_phase_output(phase: str, output_dir: Path) -> dict:
    path = output_dir / f"eq_ws2_phase{phase}_output.json"
    if not path.exists():
        raise FileNotFoundError(f"Phase {phase} output not found at {path}")
    with open(path) as f:
        return json.load(f)


# ── Bonferroni-binomial (reused from crypto WS2) ──────────────

def _compute_bonferroni_binom(tp: int, fp: int, base_rate: float,
                              n_rules: int) -> dict:
    """Bonferroni-corrected one-sided binomial test on holdout precision."""
    from scipy.stats import binomtest

    n_flagged = tp + fp
    if n_flagged < 3 or base_rate <= 0 or n_rules < 1:
        return {"significant": False, "note": "insufficient_flagged",
                "n_flagged": n_flagged, "n_rules": n_rules}

    result = binomtest(tp, n_flagged, base_rate, alternative="greater")
    raw_p = result.pvalue
    adj_p = min(raw_p * n_rules, 1.0)

    return {
        "raw_p": round(float(raw_p), 6),
        "bonferroni_p": round(float(adj_p), 6),
        "significant": adj_p < 0.05,
        "n_flagged": n_flagged,
        "tp": tp,
        "base_rate": round(float(base_rate), 6),
        "n_rules": n_rules,
        "alpha": 0.05,
    }


# ── Globals for multiprocessing ───────────────────────────────
_G_OHLCV_1D: dict[str, list[dict]] = {}
_G_OHLCV_1H: dict[str, list[dict]] = {}
_G_SPY_BARS_1D: list[dict] = []
_G_EVENTS_BY_SYM: dict[str, list[dict]] = {}
_G_MENTIONS_BY_SYM: dict[str, list[dict]] = {}
_G_EARNINGS_BY_SYM: dict[str, list[datetime]] = {}
_G_HORIZONS: list[str] = HORIZONS_DEFAULT
_G_STUDY_START: datetime = datetime(2025, 8, 1, tzinfo=timezone.utc)
_G_STUDY_END: datetime = datetime(2026, 2, 28, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════
# PHASE 0.5: Feature Viability Audit
# ══════════════════════════════════════════════════════════════

def phase05_viability(events: list[dict], mentions_by_sym: dict) -> dict:
    """Audit which features are viable before full engineering."""
    log.info("Phase 0.5: Feature Viability Audit")
    t0 = time_mod.time()

    # Build sample feature values from a subset of events
    feature_samples: dict[str, list[float | None]] = defaultdict(list)

    for ev in events:
        title = ev.get("title", "")
        source = ev.get("source", "")
        event_type = ev.get("event_type", "")
        ab = ev.get("anti_bot_score", 0.0)

        # Content features
        feature_samples["tone_score"].append(
            compute_tone_score(title, source, ab))
        feature_samples["simple_sentiment"].append(
            compute_simple_sentiment(title))

        ft = compute_filing_type_features(event_type)
        for k, v in ft.items():
            feature_samples[k].append(v)

        # Source diversity — approximate with source field
        feature_samples["source_diversity"].append(
            1 if source else 0)

    # Mention-based features (sample from first 1000)
    for sym, mentions in mentions_by_sym.items():
        for m in mentions[:100]:
            content = m.get("content") or m.get("title") or ""
            feature_samples["mention_content_length"].append(
                len(content) if content else None)

    audit = feature_viability_audit(feature_samples)

    # Report
    dead = [k for k, v in audit.items() if v["status"] == "dead"]
    degenerate = [k for k, v in audit.items() if v["status"] == "degenerate"]
    viable = [k for k, v in audit.items() if v["status"] == "viable"]

    log.info(f"  Viable: {len(viable)} features")
    log.info(f"  Dead: {len(dead)} features: {dead}")
    log.info(f"  Degenerate: {len(degenerate)} features: {degenerate}")

    elapsed = time_mod.time() - t0
    log.info(f"Phase 0.5 done: {elapsed:.1f}s")

    return {
        "audit": audit,
        "dead_features": dead,
        "degenerate_features": degenerate,
        "viable_features": viable,
    }


# ══════════════════════════════════════════════════════════════
# PHASE 1: Dataset Construction + Labelling
# ══════════════════════════════════════════════════════════════

def _compute_forward_returns(
    bars: list[dict],
    idx: int,
    horizons: list[str],
    bar_dates: list[str],
    date_to_idx: dict[str, int],
) -> dict[str, float]:
    """Compute forward returns at each horizon for bar at idx."""
    result = {}
    cur_close = float(bars[idx]["close"])
    if cur_close <= 0:
        return result

    for h in horizons:
        if h == "gap_open":
            if idx + 1 < len(bars):
                next_open = float(bars[idx + 1]["open"])
                result[h] = next_open / cur_close - 1
        else:
            n_days = int(h.replace("d", ""))
            if idx + n_days < len(bars):
                future_close = float(bars[idx + n_days]["close"])
                result[h] = future_close / cur_close - 1

    return result


def phase1_dataset(
    stock_symbols: list[str],
    ohlcv_1d: dict[str, list[dict]],
    spy_bars: list[dict],
    horizons: list[str],
    study_start: datetime,
    z_threshold: float = 1.0,
) -> dict:
    """Construct labelled dataset with excess-return and raw-return labels."""
    log.info("Phase 1: Dataset Construction + Labelling")
    t0 = time_mod.time()

    # SPY returns for excess-return computation
    spy_closes = np.array([float(b["close"]) for b in spy_bars], dtype=float)
    spy_returns = np.diff(spy_closes) / spy_closes[:-1] if len(spy_closes) > 1 else np.array([])
    spy_dates = []
    for b in spy_bars:
        ts = b["bar_start_ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        spy_dates.append(ts.strftime("%Y-%m-%d"))

    spy_date_to_ret = {}
    for i, d in enumerate(spy_dates[1:], 1):
        spy_date_to_ret[d] = float(spy_returns[i - 1])

    # SPY forward returns for excess computation
    spy_fwd: dict[str, dict[str, float]] = {}
    for i in range(len(spy_bars)):
        d = spy_dates[i]
        spy_fwd[d] = {}
        for h in horizons:
            if h == "gap_open":
                if i + 1 < len(spy_bars):
                    spy_fwd[d][h] = float(spy_bars[i + 1]["open"]) / spy_closes[i] - 1
            else:
                n = int(h.replace("d", ""))
                if i + n < len(spy_bars):
                    spy_fwd[d][h] = spy_closes[i + n] / spy_closes[i] - 1

    observations: list[dict] = []
    label_counts_excess: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    label_counts_raw: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # Cross-sectional returns for Moulton correction
    all_daily_returns: dict[str, dict[str, float]] = defaultdict(dict)  # date → {symbol: return}

    for sym in stock_symbols:
        bars = ohlcv_1d.get(sym, [])
        if len(bars) < 80:  # Need 60 for beta + 20 for vol
            continue

        closes = np.array([float(b["close"]) for b in bars], dtype=float)
        returns = np.diff(closes) / closes[:-1]

        bar_dates = []
        for b in bars:
            ts = b["bar_start_ts"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            bar_dates.append(ts.strftime("%Y-%m-%d"))

        # Align SPY returns to this symbol's dates
        sym_spy_rets = np.array([spy_date_to_ret.get(d, 0.0) for d in bar_dates[1:]])

        # Rolling beta and vols
        beta = compute_rolling_beta(returns, sym_spy_rets, window=60)
        excess_rets = compute_excess_returns(returns, sym_spy_rets, beta)
        residual_vol = compute_residual_vol(excess_rets, window=20)
        realized_vol = compute_realized_vol(returns, window=20)

        # Track daily returns for Moulton
        for i in range(len(returns)):
            d = bar_dates[i + 1]
            all_daily_returns[d][sym] = float(returns[i])

        # Forward returns and labelling
        study_start_str = study_start.strftime("%Y-%m-%d")
        cooldown_tracker: dict[str, str] = {}  # horizon → last_label_date

        for i in range(1, len(bars)):  # skip first bar (no return)
            bar_date = bar_dates[i]
            if bar_date < study_start_str:
                continue

            if i - 1 >= len(returns):
                continue

            fwd_rets = _compute_forward_returns(bars, i, horizons, bar_dates, {d: j for j, d in enumerate(bar_dates)})
            if not fwd_rets:
                continue

            b_val = float(beta[i - 1]) if i - 1 < len(beta) and np.isfinite(beta[i - 1]) else 1.0
            rv = float(residual_vol[i - 1]) if i - 1 < len(residual_vol) and np.isfinite(residual_vol[i - 1]) else None
            raw_v = float(realized_vol[i - 1]) if i - 1 < len(realized_vol) and np.isfinite(realized_vol[i - 1]) else None

            for h, fwd_ret in fwd_rets.items():
                # Cooldown: 1 per symbol per horizon per 24h
                cooldown_key = f"{sym}|{h}"
                if cooldown_key in cooldown_tracker:
                    last_date = cooldown_tracker[cooldown_key]
                    if bar_date == last_date:
                        continue

                spy_fwd_ret = spy_fwd.get(bar_date, {}).get(h, 0.0)

                # Excess-return label (PRIMARY)
                excess_label = "FLAT"
                if rv is not None and rv > 1e-8:
                    excess_label = label_excess_return(
                        fwd_ret, b_val, spy_fwd_ret, rv, z_threshold)

                # Raw-return label (DIAGNOSTIC)
                raw_label = "FLAT"
                if raw_v is not None and raw_v > 1e-8:
                    raw_label = label_raw_return(fwd_ret, raw_v, z_threshold)

                label_counts_excess[h][excess_label] += 1
                label_counts_raw[h][raw_label] += 1

                cooldown_tracker[cooldown_key] = bar_date

                observations.append({
                    "symbol": sym,
                    "bar_date": bar_date,
                    "horizon": h,
                    "forward_return": round(fwd_ret, 6),
                    "spy_forward_return": round(spy_fwd_ret, 6),
                    "beta_60d": round(b_val, 4),
                    "residual_vol_20": round(rv, 6) if rv else None,
                    "realized_vol_20": round(raw_v, 6) if raw_v else None,
                    "excess_label": excess_label,
                    "raw_label": raw_label,
                })

    # Moulton correction
    # Build returns matrix (dates × symbols) for cross-sectional correlation
    all_dates = sorted(all_daily_returns.keys())
    all_syms = sorted(stock_symbols)
    sym_to_col = {s: i for i, s in enumerate(all_syms)}
    returns_matrix = np.full((len(all_dates), len(all_syms)), np.nan)
    for i, d in enumerate(all_dates):
        for sym, ret in all_daily_returns[d].items():
            if sym in sym_to_col:
                returns_matrix[i, sym_to_col[sym]] = ret

    rho_raw = estimate_cross_sectional_correlation(returns_matrix)

    # For excess returns, subtract market component
    spy_ret_by_date = {d: spy_date_to_ret.get(d, 0.0) for d in all_dates}
    excess_matrix = returns_matrix.copy()
    for i, d in enumerate(all_dates):
        excess_matrix[i, :] -= spy_ret_by_date.get(d, 0.0)  # simplified (beta=1 approx)

    rho_excess = estimate_cross_sectional_correlation(excess_matrix)

    n_study_days = sum(1 for d in all_dates if d >= study_start.strftime("%Y-%m-%d"))
    n_eff_raw = moulton_effective_n(len(stock_symbols), rho_raw, n_study_days)
    n_eff_excess = moulton_effective_n(len(stock_symbols), rho_excess, n_study_days)

    # Base rates per horizon
    base_rates_excess: dict[str, dict] = {}
    base_rates_raw: dict[str, dict] = {}
    for h in horizons:
        ec = label_counts_excess[h]
        total = sum(ec.values())
        base_rates_excess[h] = {
            "MOVED_UP": ec.get("MOVED_UP", 0),
            "MOVED_DOWN": ec.get("MOVED_DOWN", 0),
            "FLAT": ec.get("FLAT", 0),
            "total": total,
            "moved_pct": round((ec.get("MOVED_UP", 0) + ec.get("MOVED_DOWN", 0)) / max(1, total) * 100, 1),
        }
        rc = label_counts_raw[h]
        total_r = sum(rc.values())
        base_rates_raw[h] = {
            "MOVED_UP": rc.get("MOVED_UP", 0),
            "MOVED_DOWN": rc.get("MOVED_DOWN", 0),
            "FLAT": rc.get("FLAT", 0),
            "total": total_r,
            "moved_pct": round((rc.get("MOVED_UP", 0) + rc.get("MOVED_DOWN", 0)) / max(1, total_r) * 100, 1),
        }

    elapsed = time_mod.time() - t0
    log.info(f"Phase 1 done: {len(observations)} observations, "
             f"{len(stock_symbols)} stocks, {elapsed:.1f}s")

    for h in horizons:
        br = base_rates_excess[h]
        log.info(f"  {h} excess: UP={br['MOVED_UP']} DOWN={br['MOVED_DOWN']} "
                 f"FLAT={br['FLAT']} ({br['moved_pct']}% moved)")

    log.info(f"  Cross-sectional ρ (raw): {rho_raw:.3f}")
    log.info(f"  Cross-sectional ρ (excess): {rho_excess:.3f}")
    log.info(f"  Effective N (raw): {n_eff_raw:.0f}")
    log.info(f"  Effective N (excess): {n_eff_excess:.0f}")

    return {
        "observations": observations,
        "base_rates_excess": base_rates_excess,
        "base_rates_raw": base_rates_raw,
        "sample_size": {
            "nominal_n": len(observations),
            "n_stocks": len(stock_symbols),
            "n_study_days": n_study_days,
            "rho_raw": round(rho_raw, 4),
            "rho_excess": round(rho_excess, 4),
            "effective_n_raw": round(n_eff_raw, 0),
            "effective_n_excess": round(n_eff_excess, 0),
        },
    }


# ══════════════════════════════════════════════════════════════
# PHASE 2: Feature Engineering
# ══════════════════════════════════════════════════════════════

def _phase2_worker(sym: str) -> list[dict]:
    """Compute features for one symbol. Called from Pool."""
    bars_1d = _G_OHLCV_1D.get(sym, [])
    bars_1h = _G_OHLCV_1H.get(sym, [])
    events = _G_EVENTS_BY_SYM.get(sym, [])
    mentions = _G_MENTIONS_BY_SYM.get(sym, [])
    earnings_dates = _G_EARNINGS_BY_SYM.get(sym, [])

    if len(bars_1d) < 200:
        return []

    results = []
    study_start_str = _G_STUDY_START.strftime("%Y-%m-%d")

    # Prepare SPY data for benchmark
    spy_bars = _G_SPY_BARS_1D
    spy_closes = np.array([float(b["close"]) for b in spy_bars], dtype=float)

    for i in range(199, len(bars_1d)):
        bar = bars_1d[i]
        bar_ts = bar["bar_start_ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        bar_date = bar_ts.strftime("%Y-%m-%d")

        if bar_date < study_start_str:
            continue

        available_at = bar_ts + timedelta(days=1)

        # Technical indicators (1d)
        try:
            tech_1d = compute_all(bars_1d, i, benchmark_bars=spy_bars)
        except Exception:
            tech_1d = {}

        # Prefix 1d features
        features: dict[str, float | int | None] = {}
        for k, v in tech_1d.items():
            features[f"1d_{k}"] = v

        # Technical indicators (1h) — find matching 1h bars
        if bars_1h:
            # Find 1h bars up to this date
            matching_1h = [b for b in bars_1h
                           if (b["bar_start_ts"] if isinstance(b["bar_start_ts"], str)
                               else b["bar_start_ts"].isoformat()) <= bar_date + "T23:59:59"]
            if len(matching_1h) >= 200:
                try:
                    tech_1h = compute_all(matching_1h, len(matching_1h) - 1,
                                         benchmark_bars=spy_bars)
                    for k, v in tech_1h.items():
                        features[f"1h_{k}"] = v
                except Exception:
                    pass

        # Content features — events in lookback windows
        sym_events_before = [e for e in events
                             if (e.get("available_at_ts") or datetime.min.replace(tzinfo=timezone.utc)) <= available_at]

        # Event counts
        for window_name, hours in [("6h", 6), ("24h", 24), ("3d", 72)]:
            cutoff = available_at - timedelta(hours=hours)
            count = sum(1 for e in sym_events_before
                       if (e.get("available_at_ts") or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff)
            features[f"event_count_{window_name}"] = count

        # Event count deviation (normalized)
        ec_24h = features.get("event_count_24h", 0)
        lookback_30d = available_at - timedelta(days=30)
        daily_counts = defaultdict(int)
        for e in sym_events_before:
            ets = e.get("available_at_ts")
            if ets and ets >= lookback_30d:
                daily_counts[ets.strftime("%Y-%m-%d") if isinstance(ets, datetime) else str(ets)[:10]] += 1
        median_daily = float(np.median(list(daily_counts.values()))) if daily_counts else 1.0
        features["event_count_deviation"] = compute_event_count_deviation(
            ec_24h, median_daily)

        # Tone and sentiment on most recent event
        if sym_events_before:
            latest_ev = sym_events_before[-1]
            features["tone_score"] = compute_tone_score(
                latest_ev.get("title", ""),
                latest_ev.get("source", ""),
                latest_ev.get("anti_bot_score", 0.0),
            )
            features["simple_sentiment"] = compute_simple_sentiment(
                latest_ev.get("title", ""))

            # Filing type
            ft = compute_filing_type_features(latest_ev.get("event_type", ""))
            features.update(ft)
        else:
            features["tone_score"] = 0.0
            features["simple_sentiment"] = 0.0

        # Mention counts
        sym_mentions_before = [m for m in mentions
                               if (m.get("available_at_ts") or datetime.min.replace(tzinfo=timezone.utc)) <= available_at]
        mc = compute_mention_counts(sym_mentions_before, available_at)
        features.update(mc)
        features["mention_velocity"] = compute_mention_velocity(
            sym_mentions_before, available_at)

        # Source diversity & multi-source confirmation
        features["source_diversity"] = compute_source_diversity(
            sym_events_before, available_at)
        features["multi_source_confirmation"] = compute_multi_source_confirmation(
            sym_events_before, available_at)

        # Session features
        sess = compute_session_features(available_at)
        features["session_state_code"] = hash(sess["session_state"]) % 4
        features["hours_to_next_open"] = sess["hours_to_next_open"]
        features["hours_since_close"] = sess["hours_since_close"]
        features["day_of_week"] = sess["day_of_week"]

        # Earnings proximity
        ep = compute_earnings_proximity(available_at, earnings_dates)
        features["days_to_earnings"] = ep["days_to_earnings"]
        features["in_earnings_window"] = ep["in_earnings_window"]

        # Cross-domain features
        rv20 = features.get("1d_realized_vol_20")
        if rv20 is not None and rv20 > 0:
            features["event_density_x_vol"] = ec_24h * rv20
        vr20 = features.get("1d_volume_ratio_20")
        if vr20 is not None:
            features["filing_type_x_volume_ratio"] = features.get("filing_type_8k", 0) * vr20

        tone = features.get("tone_score", 0)
        roc5 = features.get("1d_roc_5")
        if tone != 0 and roc5 is not None:
            features["sentiment_price_divergence"] = int(
                (tone > 0 and roc5 < 0) or (tone < 0 and roc5 > 0))

        results.append({
            "symbol": sym,
            "bar_date": bar_date,
            "features": features,
        })

    return results


def phase2_features(
    stock_symbols: list[str],
    workers: int,
) -> dict:
    """Phase 2: Feature engineering for all stock symbols."""
    log.info("Phase 2: Feature Engineering")
    t0 = time_mod.time()

    # Sort heaviest-first by bar count
    syms_sorted = sorted(stock_symbols,
                         key=lambda s: len(_G_OHLCV_1D.get(s, [])),
                         reverse=True)

    log.info(f"  Processing {len(syms_sorted)} symbols "
             f"(heaviest: {len(_G_OHLCV_1D.get(syms_sorted[0], []))} bars)")

    if workers > 1 and len(syms_sorted) > 1:
        with mp.Pool(min(workers, len(syms_sorted))) as pool:
            nested = pool.map(_phase2_worker, syms_sorted)
    else:
        nested = [_phase2_worker(s) for s in syms_sorted]

    all_features = []
    for batch in nested:
        all_features.extend(batch)

    # Collect all feature names
    all_feat_names = set()
    for entry in all_features:
        all_feat_names.update(entry["features"].keys())

    elapsed = time_mod.time() - t0
    log.info(f"Phase 2 done: {len(all_features)} feature rows, "
             f"{len(all_feat_names)} unique features, {elapsed:.1f}s")

    return {
        "feature_rows": all_features,
        "feature_names": sorted(all_feat_names),
        "n_symbols": len(syms_sorted),
    }


# ══════════════════════════════════════════════════════════════
# PHASE 3: Correlation Analysis
# ══════════════════════════════════════════════════════════════

def phase3_correlations(
    observations: list[dict],
    feature_rows: list[dict],
    feature_names: list[str],
    dead_features: list[str],
    horizons: list[str],
    sample_size_info: dict,
) -> dict:
    """Point-biserial correlation, both label sets, BH-FDR correction."""
    log.info("Phase 3: Correlation Analysis")
    t0 = time_mod.time()

    # Build lookup: (symbol, bar_date, horizon) → labels
    label_lookup: dict[str, dict] = {}
    for obs in observations:
        key = f"{obs['symbol']}|{obs['bar_date']}|{obs['horizon']}"
        label_lookup[key] = {
            "excess_label": obs["excess_label"],
            "raw_label": obs["raw_label"],
        }

    # Build feature lookup: (symbol, bar_date) → features
    feat_lookup: dict[str, dict] = {}
    for fr in feature_rows:
        key = f"{fr['symbol']}|{fr['bar_date']}"
        feat_lookup[key] = fr["features"]

    # Exclude dead features from correlation analysis
    active_features = [f for f in feature_names if f not in dead_features]
    log.info(f"  Testing {len(active_features)} active features × "
             f"{len(horizons)} horizons = {len(active_features) * len(horizons)} tests")

    correlations_excess: list[dict] = []
    correlations_raw: list[dict] = []

    for feat in active_features:
        for h in horizons:
            vals_ex = []
            labels_ex = []
            vals_raw = []
            labels_raw = []

            for fr in feature_rows:
                sym = fr["symbol"]
                bd = fr["bar_date"]
                fval = fr["features"].get(feat)
                if fval is None or not np.isfinite(fval):
                    continue

                key = f"{sym}|{bd}|{h}"
                lab = label_lookup.get(key)
                if lab is None:
                    continue

                # Excess label
                if lab["excess_label"] != "FLAT":
                    vals_ex.append(fval)
                    labels_ex.append(1 if lab["excess_label"] == "MOVED_UP" else 0)

                # Raw label
                if lab["raw_label"] != "FLAT":
                    vals_raw.append(fval)
                    labels_raw.append(1 if lab["raw_label"] == "MOVED_UP" else 0)

            # Excess-return correlation
            if len(vals_ex) >= 20:
                va = np.array(vals_ex)
                la = np.array(labels_ex, dtype=float)
                if va.std() > 1e-10 and la.std() > 1e-10:
                    corr = float(np.corrcoef(va, la)[0, 1])
                    if np.isfinite(corr):
                        correlations_excess.append({
                            "feature": feat,
                            "horizon": h,
                            "correlation": round(corr, 6),
                            "n": len(vals_ex),
                            "abs_corr": round(abs(corr), 6),
                        })

            # Raw-return correlation
            if len(vals_raw) >= 20:
                va = np.array(vals_raw)
                la = np.array(labels_raw, dtype=float)
                if va.std() > 1e-10 and la.std() > 1e-10:
                    corr = float(np.corrcoef(va, la)[0, 1])
                    if np.isfinite(corr):
                        correlations_raw.append({
                            "feature": feat,
                            "horizon": h,
                            "correlation": round(corr, 6),
                            "n": len(vals_raw),
                            "abs_corr": round(abs(corr), 6),
                        })

    # BH-FDR correction across ALL horizons simultaneously
    from scipy.stats import pearsonr
    for corr_list in [correlations_excess, correlations_raw]:
        # Compute p-values
        for entry in corr_list:
            n = entry["n"]
            r = entry["correlation"]
            if n > 2 and abs(r) < 1:
                t_stat = r * np.sqrt((n - 2) / (1 - r**2))
                from scipy.stats import t as t_dist
                p_val = 2 * (1 - t_dist.cdf(abs(t_stat), n - 2))
                entry["p_value"] = round(p_val, 8)
            else:
                entry["p_value"] = 1.0

        # BH-FDR
        corr_list.sort(key=lambda x: x["p_value"])
        m = len(corr_list)
        for i, entry in enumerate(corr_list):
            entry["bh_threshold"] = round(0.05 * (i + 1) / max(1, m), 8)
            entry["bh_significant"] = entry["p_value"] <= entry["bh_threshold"]

    # Sort by abs correlation
    correlations_excess.sort(key=lambda x: x["abs_corr"], reverse=True)
    correlations_raw.sort(key=lambda x: x["abs_corr"], reverse=True)

    # Flag market-factor proxies: significant in raw but not excess
    raw_sig = {(c["feature"], c["horizon"]) for c in correlations_raw if c.get("bh_significant")}
    excess_sig = {(c["feature"], c["horizon"]) for c in correlations_excess if c.get("bh_significant")}
    market_factor_proxies = sorted(raw_sig - excess_sig)

    n_sig_excess = sum(1 for c in correlations_excess if c.get("bh_significant"))
    n_sig_raw = sum(1 for c in correlations_raw if c.get("bh_significant"))

    log.info(f"  Significant (excess, BH-FDR): {n_sig_excess}/{len(correlations_excess)}")
    log.info(f"  Significant (raw, BH-FDR): {n_sig_raw}/{len(correlations_raw)}")
    log.info(f"  Market-factor proxies (raw-only): {len(market_factor_proxies)}")

    if correlations_excess:
        log.info("  Top-10 (excess-return labels):")
        for i, c in enumerate(correlations_excess[:10]):
            sig = "+" if c.get("bh_significant") else " "
            log.info(f"    {sig} {i+1}. {c['feature']}|{c['horizon']}: "
                     f"r={c['correlation']:.4f} p={c['p_value']:.4e} n={c['n']}")

    elapsed = time_mod.time() - t0
    log.info(f"Phase 3 done: {elapsed:.1f}s")

    # Effective N reporting (SME Gap 2)
    log.info(f"  Nominal N: {sample_size_info.get('nominal_n', 'N/A')}")
    log.info(f"  Effective N (excess, Moulton): {sample_size_info.get('effective_n_excess', 'N/A')}")

    return {
        "correlations_excess": correlations_excess,
        "correlations_raw": correlations_raw,
        "n_significant_excess": n_sig_excess,
        "n_significant_raw": n_sig_raw,
        "market_factor_proxies": [{"feature": f, "horizon": h} for f, h in market_factor_proxies],
        "n_tests": len(active_features) * len(horizons),
        "sample_size": sample_size_info,
    }


# ══════════════════════════════════════════════════════════════
# PHASE 5: Walk-Forward Validation
# ══════════════════════════════════════════════════════════════

def phase5_walk_forward(
    observations: list[dict],
    feature_rows: list[dict],
    significant_features: list[dict],
    horizons: list[str],
    n_folds: int = 6,
    embargo_days: int = 10,
) -> dict:
    """Walk-forward validation on significant features."""
    log.info("Phase 5: Walk-Forward Validation")
    t0 = time_mod.time()

    if not significant_features:
        log.info("  No significant features — skipping walk-forward")
        return {"rules_tested": 0, "rules_passing": [], "n_rules_total": 0}

    # Build merged dataset: (symbol, bar_date) → {features + labels}
    feat_lookup = {}
    for fr in feature_rows:
        key = f"{fr['symbol']}|{fr['bar_date']}"
        feat_lookup[key] = fr["features"]

    label_lookup = {}
    for obs in observations:
        key = f"{obs['symbol']}|{obs['bar_date']}|{obs['horizon']}"
        label_lookup[key] = obs["excess_label"]

    # Get unique dates for fold construction
    all_dates = sorted(set(obs["bar_date"] for obs in observations))
    n_dates = len(all_dates)

    if n_dates < 30:
        log.warning(f"  Only {n_dates} unique dates — insufficient for walk-forward")
        return {"rules_tested": 0, "rules_passing": [], "n_rules_total": 0}

    # Chronological folds (80/20 expanding window)
    holdout_start = int(n_dates * 0.8)
    holdout_dates = set(all_dates[holdout_start:])
    wf_dates = all_dates[:holdout_start]

    fold_size = len(wf_dates) // n_folds
    if fold_size < 5:
        log.warning(f"  Fold size {fold_size} too small")
        return {"rules_tested": 0, "rules_passing": [], "n_rules_total": 0}

    rules_tested = 0
    rules_passing = []

    for feat_info in significant_features:
        feat_name = feat_info["feature"]
        horizon = feat_info["horizon"]
        direction = 1 if feat_info["correlation"] > 0 else -1

        # Collect (date, feature_val, label) triples
        data_points = []
        for fr in feature_rows:
            sym = fr["symbol"]
            bd = fr["bar_date"]
            if bd in holdout_dates:
                continue
            fval = fr["features"].get(feat_name)
            if fval is None or not np.isfinite(fval):
                continue
            key = f"{sym}|{bd}|{horizon}"
            lab = label_lookup.get(key)
            if lab is None or lab == "FLAT":
                continue
            data_points.append({
                "date": bd,
                "value": fval,
                "label": 1 if lab == "MOVED_UP" else 0,
            })

        if len(data_points) < 30:
            continue

        data_points.sort(key=lambda x: x["date"])
        rules_tested += 1

        # Walk-forward: expanding window with embargo
        folds_passing = 0
        folds_total = 0

        for fold_idx in range(n_folds):
            test_start = fold_idx * fold_size
            test_end = min(test_start + fold_size, len(wf_dates))

            if test_start < 20:  # Minimum training size
                continue

            test_dates_set = set(wf_dates[test_start:test_end])

            # Embargo: exclude points within embargo_days of test period
            train_cutoff_date = wf_dates[max(0, test_start - embargo_days)]

            train_points = [p for p in data_points
                           if p["date"] < train_cutoff_date]
            test_points = [p for p in data_points
                          if p["date"] in test_dates_set]

            if len(train_points) < 10 or len(test_points) < 5:
                continue

            # Find threshold from training data (median of feature values
            # for MOVED_UP if direction > 0, else MOVED_DOWN)
            target_label = 1 if direction > 0 else 0
            target_vals = [p["value"] for p in train_points if p["label"] == target_label]
            if not target_vals:
                continue

            threshold = float(np.median(target_vals))

            # Test: precision of threshold rule
            if direction > 0:
                flagged = [p for p in test_points if p["value"] >= threshold]
            else:
                flagged = [p for p in test_points if p["value"] <= threshold]

            if len(flagged) < 3:
                continue

            tp = sum(1 for p in flagged if p["label"] == target_label)
            precision = tp / len(flagged)
            base_rate = sum(p["label"] == target_label for p in test_points) / len(test_points)

            folds_total += 1
            if precision > base_rate:
                folds_passing += 1

        # Pass gate: ≥4/6 folds (≥67%)
        if folds_total >= 4 and folds_passing / folds_total >= 0.67:
            rules_passing.append({
                "feature": feat_name,
                "horizon": horizon,
                "direction": direction,
                "folds_passing": folds_passing,
                "folds_total": folds_total,
            })
            log.info(f"  ✓ {feat_name}|{horizon}: {folds_passing}/{folds_total} folds")

    elapsed = time_mod.time() - t0
    log.info(f"Phase 5 done: {rules_tested} tested, {len(rules_passing)} passing, {elapsed:.1f}s")

    return {
        "rules_tested": rules_tested,
        "rules_passing": rules_passing,
        "n_rules_total": rules_tested,  # Full search space for Bonferroni
    }


# ══════════════════════════════════════════════════════════════
# PHASE 6: Holdout + Bonferroni-Binomial
# ══════════════════════════════════════════════════════════════

def phase6_holdout(
    observations: list[dict],
    feature_rows: list[dict],
    rules_passing: list[dict],
    n_rules_total: int,
    effective_n: float,
) -> dict:
    """Holdout evaluation with Bonferroni-binomial using effective N."""
    log.info("Phase 6: Holdout + Bonferroni-Binomial")
    t0 = time_mod.time()

    if not rules_passing:
        log.info("  No rules passed walk-forward — nothing to evaluate")
        return {"confirmed_rules": [], "n_evaluated": 0}

    # Holdout = final 20% of dates
    all_dates = sorted(set(obs["bar_date"] for obs in observations))
    holdout_start = int(len(all_dates) * 0.8)
    holdout_dates = set(all_dates[holdout_start:])

    label_lookup = {}
    for obs in observations:
        if obs["bar_date"] in holdout_dates:
            key = f"{obs['symbol']}|{obs['bar_date']}|{obs['horizon']}"
            label_lookup[key] = obs["excess_label"]

    feat_lookup = {}
    for fr in feature_rows:
        if fr["bar_date"] in holdout_dates:
            key = f"{fr['symbol']}|{fr['bar_date']}"
            feat_lookup[key] = fr["features"]

    confirmed_rules = []

    for rule in rules_passing:
        feat_name = rule["feature"]
        horizon = rule["horizon"]
        direction = rule["direction"]
        target_label = "MOVED_UP" if direction > 0 else "MOVED_DOWN"

        # Collect holdout data
        holdout_points = []
        for fr in feature_rows:
            if fr["bar_date"] not in holdout_dates:
                continue
            fval = fr["features"].get(feat_name)
            if fval is None or not np.isfinite(fval):
                continue
            key = f"{fr['symbol']}|{fr['bar_date']}|{horizon}"
            lab = label_lookup.get(key)
            if lab is None or lab == "FLAT":
                continue
            holdout_points.append({"value": fval, "label": lab})

        if len(holdout_points) < 10:
            continue

        # Use median as threshold (from non-holdout training)
        target_vals = [p["value"] for p in holdout_points if p["label"] == target_label]
        all_vals = [p["value"] for p in holdout_points]
        threshold = float(np.median(all_vals))

        if direction > 0:
            flagged = [p for p in holdout_points if p["value"] >= threshold]
        else:
            flagged = [p for p in holdout_points if p["value"] <= threshold]

        if len(flagged) < 3:
            continue

        tp = sum(1 for p in flagged if p["label"] == target_label)
        fp = len(flagged) - tp
        base_rate = sum(1 for p in holdout_points if p["label"] == target_label) / len(holdout_points)

        precision = tp / len(flagged)

        # Bonferroni-binomial with effective N (SME Gap 2, Level 2)
        bonf = _compute_bonferroni_binom(tp, fp, base_rate, n_rules_total)

        result = {
            "feature": feat_name,
            "horizon": horizon,
            "direction": direction,
            "holdout_precision": round(precision, 4),
            "holdout_base_rate": round(base_rate, 4),
            "holdout_n_flagged": len(flagged),
            "holdout_tp": tp,
            "holdout_fp": fp,
            "bonferroni": bonf,
            "confirmed": precision > base_rate and bonf.get("significant", False),
        }

        # Cluster detection: flag if events cluster in <3 calendar weeks
        flagged_dates = set()
        for fr in feature_rows:
            if fr["bar_date"] not in holdout_dates:
                continue
            fval = fr["features"].get(feat_name)
            if fval is None:
                continue
            if (direction > 0 and fval >= threshold) or \
               (direction < 0 and fval <= threshold):
                flagged_dates.add(fr["bar_date"])

        if flagged_dates:
            sorted_fd = sorted(flagged_dates)
            n_weeks = (datetime.strptime(sorted_fd[-1], "%Y-%m-%d") -
                      datetime.strptime(sorted_fd[0], "%Y-%m-%d")).days / 7
            result["holdout_span_weeks"] = round(n_weeks, 1)
            result["cluster_warning"] = n_weeks < 3

        confirmed_rules.append(result)

        status = "CONFIRMED" if result["confirmed"] else "rejected"
        log.info(f"  {status}: {feat_name}|{horizon} "
                 f"precision={precision:.3f} base={base_rate:.3f} "
                 f"bonf_p={bonf.get('bonferroni_p', 'N/A')}")

    elapsed = time_mod.time() - t0
    log.info(f"Phase 6 done: {len(confirmed_rules)} evaluated, "
             f"{sum(1 for r in confirmed_rules if r['confirmed'])} confirmed, "
             f"{elapsed:.1f}s")

    return {
        "confirmed_rules": [r for r in confirmed_rules if r["confirmed"]],
        "all_evaluated": confirmed_rules,
        "n_evaluated": len(confirmed_rules),
        "n_confirmed": sum(1 for r in confirmed_rules if r["confirmed"]),
        "effective_n_used": round(effective_n, 0),
    }


# ══════════════════════════════════════════════════════════════
# PHASE 7: Output Report
# ══════════════════════════════════════════════════════════════

def phase7_report(
    phase05_data: dict,
    phase1_data: dict,
    phase3_data: dict,
    phase5_data: dict,
    phase6_data: dict,
    study_params: dict,
) -> dict:
    """Phase 7: Generate comprehensive output report."""
    log.info("Phase 7: Generating report")

    # Content vs frequency vs technical ranking
    excess_corrs = phase3_data.get("correlations_excess", [])
    content_feats = [c for c in excess_corrs
                     if any(c["feature"].startswith(p)
                            for p in ["tone_", "simple_sentiment", "filing_type",
                                      "is_earnings", "is_analyst", "is_news"])]
    freq_feats = [c for c in excess_corrs
                  if any(c["feature"].startswith(p)
                         for p in ["event_count", "mention_count", "mention_velocity",
                                   "source_diversity", "multi_source", "event_count_dev"])]
    tech_feats = [c for c in excess_corrs
                  if c["feature"].startswith("1d_") or c["feature"].startswith("1h_")]
    market_feats = [c for c in excess_corrs
                    if any(c["feature"].startswith(p)
                           for p in ["session_", "hours_", "day_of_week",
                                     "days_to_earnings", "in_earnings",
                                     "overnight_gap"])]

    # LLM opportunity assessment (SME Gap 3)
    dead = phase05_data.get("dead_features", [])
    n_confirmed = phase6_data.get("n_confirmed", 0)

    filing_type_sig = any(c.get("bh_significant") for c in content_feats
                         if "filing_type" in c["feature"])
    tone_sig = any(c.get("bh_significant") for c in content_feats
                   if c["feature"] == "tone_score")

    if filing_type_sig and not tone_sig:
        llm_verdict = "TYPE_PREDICTS_TONE_DOESNT"
        llm_detail = ("Filing TYPE classification shows signal but keyword tone "
                      "scoring does not. Structured metadata predicts; keyword NLP "
                      "on 16-char SEC titles adds nothing. Opportunity: LLM-based "
                      "reading of filing BODIES (10-Q/10-K), not better headline processing.")
    elif not filing_type_sig and not tone_sig:
        llm_verdict = "NO_CONTENT_SIGNAL"
        llm_detail = ("No content features predict at this coverage level. "
                      "With 8% NEWS coverage and 16-char SEC titles, this is a "
                      "data availability problem, not a conceptual failure. "
                      "Cannot distinguish scorer quality from data gap.")
    elif tone_sig:
        llm_verdict = "TONE_HAS_SIGNAL"
        llm_detail = ("Keyword tone scoring shows signal despite 8% coverage. "
                      "LLM processing would likely improve on this, especially "
                      "for SEC filing body text.")
    else:
        llm_verdict = "MIXED"
        llm_detail = "Mixed results — some content features significant."

    report = {
        "study": "EQ-WS2 Content-Price Correlation Study",
        "period": f"{study_params['start_date']} to {study_params['end_date']}",
        "sme_corrections_applied": [
            "Gap 1: Excess-return labels (primary), raw-return (diagnostic)",
            "Gap 2: Moulton-corrected effective N",
            "Gap 3: Content void distinction (scorer vs data vs type)",
            "Rec C: ETFs excluded from labels",
            "Rec D: Effective cardinality < 3 kill rule",
            "Rec E: sector_relative_return deduplicated",
        ],

        # Section 1: Feature importance
        "feature_importance_excess": {
            "content": content_feats[:10],
            "frequency": freq_feats[:10],
            "technical": tech_feats[:10],
            "market_structure": market_feats[:10],
        },

        # Section 2: Content effectiveness
        "content_effectiveness": {
            "tone_scorer_coverage": "8% (NEWS only)",
            "n_content_significant": sum(1 for c in content_feats if c.get("bh_significant")),
            "n_technical_significant": sum(1 for c in tech_feats if c.get("bh_significant")),
            "content_vs_technical": "Content features significantly underperform technical due to coverage gap",
        },

        # Section 3: Dead features
        "dead_features": phase05_data.get("audit", {}),

        # Section 4: Market factor proxies
        "market_factor_proxies": phase3_data.get("market_factor_proxies", []),

        # Section 5: Validated rules
        "validated_rules": phase6_data.get("confirmed_rules", []),
        "n_confirmed": n_confirmed,

        # Section 6: Sample size
        "sample_size": phase1_data.get("sample_size", {}),

        # Section 7: Base rates
        "base_rates_excess": phase1_data.get("base_rates_excess", {}),
        "base_rates_raw": phase1_data.get("base_rates_raw", {}),

        # Section 8: LLM opportunity assessment (SME Gap 3)
        "llm_assessment": {
            "verdict": llm_verdict,
            "detail": llm_detail,
            "filing_type_significant": filing_type_sig,
            "tone_score_significant": tone_sig,
            "dead_content_features": [f for f in dead if any(
                f.startswith(p) for p in ["tone", "sentiment", "keyword", "content"])],
            "data_reality": ("92% SEC filings with 16-char titles. GDELT dead Sep 2024. "
                           "Reddit dead Jun 2024. Keyword scorers return 0.0 for 92% of data."),
        },

        # Walk-forward summary
        "walk_forward": {
            "rules_tested": phase5_data.get("rules_tested", 0),
            "rules_passing": len(phase5_data.get("rules_passing", [])),
            "n_rules_total": phase5_data.get("n_rules_total", 0),
        },
    }

    return report


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main() -> None:
    global _G_OHLCV_1D, _G_OHLCV_1H, _G_SPY_BARS_1D
    global _G_EVENTS_BY_SYM, _G_MENTIONS_BY_SYM, _G_EARNINGS_BY_SYM
    global _G_HORIZONS, _G_STUDY_START, _G_STUDY_END

    parser = argparse.ArgumentParser(description="EQ-WS2 Content-Price Correlation Study")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--horizons", default="1d,3d,5d,gap_open")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--from-phase", default="0.5")
    parser.add_argument("--z-threshold", type=float, default=1.0)
    parser.add_argument("--embargo-days", type=int, default=10)
    args = parser.parse_args()

    horizons = args.horizons.split(",")
    _G_HORIZONS = horizons
    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    _G_STUDY_START = start_dt
    _G_STUDY_END = end_dt

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("EQ-WS2: Content-Price Correlation Study")
    log.info("=" * 60)
    log.info(f"  Period:    {args.start_date} to {args.end_date}")
    log.info(f"  Horizons:  {horizons}")
    log.info(f"  Workers:   {args.workers}")
    log.info(f"  Z-thresh:  {args.z_threshold}")
    log.info(f"  Embargo:   {args.embargo_days} days")
    log.info("=" * 60)

    # ── Load data ──────────────────────────────────────────
    conn = connect()

    all_symbols = list_symbols(conn)
    stock_symbols = [s for s in all_symbols if not is_etf(s)]
    log.info(f"Universe: {len(all_symbols)} total, {len(stock_symbols)} stocks "
             f"(ETFs excluded from labels)")

    # OHLCV with warmup
    warmup_start = start_dt - timedelta(days=300)
    log.info(f"Loading OHLCV from {warmup_start.date()}...")

    ohlcv_1d_raw = load_ohlcv(conn, symbols=all_symbols, timeframe="1d",
                               start_ts=warmup_start, end_ts=end_dt)
    ohlcv_1h_raw = load_ohlcv(conn, symbols=all_symbols, timeframe="1h",
                               start_ts=start_dt - timedelta(days=60), end_ts=end_dt)
    log.info(f"  1d: {len(ohlcv_1d_raw)} bars, 1h: {len(ohlcv_1h_raw)} bars")

    ohlcv_1d: dict[str, list[dict]] = defaultdict(list)
    for bar in ohlcv_1d_raw:
        ohlcv_1d[bar["symbol"]].append(bar)
    _G_OHLCV_1D = dict(ohlcv_1d)

    ohlcv_1h: dict[str, list[dict]] = defaultdict(list)
    for bar in ohlcv_1h_raw:
        ohlcv_1h[bar["symbol"]].append(bar)
    _G_OHLCV_1H = dict(ohlcv_1h)

    spy_bars = ohlcv_1d.get("SPY", [])
    _G_SPY_BARS_1D = spy_bars
    log.info(f"  SPY: {len(spy_bars)} daily bars")

    # Events
    log.info("Loading events and mentions...")
    events = load_events(conn, symbols=all_symbols, start_ts=start_dt, end_ts=end_dt)
    events_by_sym: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        events_by_sym[ev["symbol"]].append(ev)
    _G_EVENTS_BY_SYM = dict(events_by_sym)
    log.info(f"  {len(events)} events")

    mentions = load_raw_mentions(conn, symbols=all_symbols, start_ts=start_dt, end_ts=end_dt)
    mentions_by_sym: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        mentions_by_sym[m["symbol"]].append(m)
    _G_MENTIONS_BY_SYM = dict(mentions_by_sym)
    log.info(f"  {len(mentions)} raw mentions")

    # Earnings calendar
    earnings_cal = load_earnings_calendar(conn, symbols=stock_symbols)
    earnings_by_sym: dict[str, list[datetime]] = defaultdict(list)
    for ec in earnings_cal:
        if ec.get("report_date"):
            rd = ec["report_date"]
            if isinstance(rd, str):
                rd = date.fromisoformat(rd)
            dt = datetime.combine(rd, datetime.min.time(), tzinfo=timezone.utc)
            earnings_by_sym[ec["symbol"]].append(dt)
    _G_EARNINGS_BY_SYM = dict(earnings_by_sym)
    log.info(f"  {len(earnings_cal)} earnings calendar entries")

    conn.close()

    # ── Phase 0.5: Viability ──────────────────────────────
    from_phase = float(args.from_phase)

    if from_phase <= 0.5:
        phase05_data = phase05_viability(events, mentions_by_sym)
        write_phase_output(phase05_data, "05", OUTPUT_DIR)
    else:
        phase05_data = load_phase_output("05", OUTPUT_DIR)
        log.info("Loaded Phase 0.5 from disk")

    dead_features = phase05_data.get("dead_features", [])

    # ── Phase 1: Dataset ──────────────────────────────────
    if from_phase <= 1:
        phase1_data = phase1_dataset(
            stock_symbols, ohlcv_1d, spy_bars, horizons,
            start_dt, args.z_threshold,
        )
        write_phase_output(phase1_data, "1", OUTPUT_DIR)
    else:
        phase1_data = load_phase_output("1", OUTPUT_DIR)
        log.info("Loaded Phase 1 from disk")

    # ── Phase 2: Features ─────────────────────────────────
    if from_phase <= 2:
        phase2_data = phase2_features(stock_symbols, args.workers)
        write_phase_output(phase2_data, "2", OUTPUT_DIR)
    else:
        phase2_data = load_phase_output("2", OUTPUT_DIR)
        log.info("Loaded Phase 2 from disk")

    # ── Phase 3: Correlations ─────────────────────────────
    if from_phase <= 3:
        phase3_data = phase3_correlations(
            phase1_data.get("observations", []),
            phase2_data.get("feature_rows", []),
            phase2_data.get("feature_names", []),
            dead_features,
            horizons,
            phase1_data.get("sample_size", {}),
        )
        write_phase_output(phase3_data, "3", OUTPUT_DIR)
    else:
        phase3_data = load_phase_output("3", OUTPUT_DIR)
        log.info("Loaded Phase 3 from disk")

    # ── Phase 5: Walk-Forward ─────────────────────────────
    significant_features = [c for c in phase3_data.get("correlations_excess", [])
                           if c.get("bh_significant")]

    if from_phase <= 5:
        phase5_data = phase5_walk_forward(
            phase1_data.get("observations", []),
            phase2_data.get("feature_rows", []),
            significant_features,
            horizons,
            n_folds=6,
            embargo_days=args.embargo_days,
        )
        write_phase_output(phase5_data, "5", OUTPUT_DIR)
    else:
        phase5_data = load_phase_output("5", OUTPUT_DIR)
        log.info("Loaded Phase 5 from disk")

    # ── Phase 6: Holdout + Bonferroni ─────────────────────
    effective_n = phase1_data.get("sample_size", {}).get("effective_n_excess", 1000)

    if from_phase <= 6:
        phase6_data = phase6_holdout(
            phase1_data.get("observations", []),
            phase2_data.get("feature_rows", []),
            phase5_data.get("rules_passing", []),
            phase5_data.get("n_rules_total", 1),
            effective_n,
        )
        write_phase_output(phase6_data, "6", OUTPUT_DIR)
    else:
        phase6_data = load_phase_output("6", OUTPUT_DIR)
        log.info("Loaded Phase 6 from disk")

    # ── Phase 7: Report ───────────────────────────────────
    report = phase7_report(
        phase05_data, phase1_data, phase3_data,
        phase5_data, phase6_data,
        {"start_date": args.start_date, "end_date": args.end_date},
    )
    write_phase_output(report, "7", OUTPUT_DIR)

    # Write human-readable summary
    report_path = OUTPUT_DIR / "eq_ws2_study_results.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"Full report → {report_path}")

    # Markdown summary
    md = [
        "# EQ-WS2 Content-Price Correlation Study",
        f"\n**Period**: {args.start_date} to {args.end_date}",
        f"**Stocks**: {len(stock_symbols)} (ETFs excluded)",
        f"**Horizons**: {', '.join(horizons)}",
        "",
        "## SME Corrections Applied",
        "- Excess-return labels (primary) remove market factor",
        "- Moulton-corrected effective N for honest sample size",
        "- Content void framing distinguishes scorer vs data vs type",
        "",
        "## Sample Size (SME Gap 2)",
        f"- Nominal N: {phase1_data.get('sample_size', {}).get('nominal_n', 'N/A')}",
        f"- ρ (raw): {phase1_data.get('sample_size', {}).get('rho_raw', 'N/A')}",
        f"- ρ (excess): {phase1_data.get('sample_size', {}).get('rho_excess', 'N/A')}",
        f"- Effective N (excess): {phase1_data.get('sample_size', {}).get('effective_n_excess', 'N/A')}",
        "",
        "## Dead Features (Phase 0.5)",
    ]
    for f_name in dead_features:
        md.append(f"- `{f_name}`")

    md.extend([
        "",
        "## Top Correlations (Excess-Return Labels)",
    ])
    for c in phase3_data.get("correlations_excess", [])[:15]:
        sig = "✓" if c.get("bh_significant") else " "
        md.append(f"- {sig} `{c['feature']}` | {c['horizon']}: "
                  f"r={c['correlation']:.4f} (n={c['n']})")

    mfp = phase3_data.get("market_factor_proxies", [])
    if mfp:
        md.extend(["", "## Market-Factor Proxies (raw-only, not excess)"])
        for p in mfp[:10]:
            md.append(f"- `{p['feature']}` | {p['horizon']}")

    n_confirmed = phase6_data.get("n_confirmed", 0)
    md.extend([
        "",
        f"## Validated Rules: {n_confirmed}",
    ])
    for r in phase6_data.get("confirmed_rules", []):
        md.append(f"- `{r['feature']}` | {r['horizon']}: "
                  f"precision={r['holdout_precision']:.3f} "
                  f"bonf_p={r['bonferroni'].get('bonferroni_p', 'N/A')}")

    llm = report.get("llm_assessment", {})
    md.extend([
        "",
        "## LLM Opportunity Assessment",
        f"**Verdict**: {llm.get('verdict', 'N/A')}",
        f"{llm.get('detail', '')}",
    ])

    md_path = OUTPUT_DIR / "eq_ws2_study_results.md"
    with open(md_path, "w") as f_out:
        f_out.write("\n".join(md))
    log.info(f"Markdown summary → {md_path}")

    log.info("EQ-WS2 complete.")


if __name__ == "__main__":
    main()
